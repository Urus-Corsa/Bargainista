let player = {
  /*
    upon initilization of player object as well as its 'move' property,
    we set it to //* null == intentionally want it to be empty rather than using 'undefined'
  */
  move: null,
  score: parseInt(localStorage.getItem('playerScore'), 10) || 0
};
let computer = {
  move: null,
  'score': parseInt(localStorage.getItem('computerScore'), 10) || 0
};
let gameStats = {
  ['tiedRounds']: parseInt(localStorage.getItem('ties'), 10) || 0,
  // when explicitly defining a method, it can also be done using //* shorthand method shortcut
  // show: function showStats(){
  show() {
    const scoreBoard = `-------- SCORE BOARD --------\nYour Score: ${player.score}\nComputer's Score: ${computer.score}\nTied Rounds: ${this.tiedRounds}`;
    return scoreBoard;
  },
  reset: function resetGameStats(){ //* functions stored inside of an object == methods
    // reset local storage objects
    //! clearing the entire local storage objects may result in other stored data to be lost as well, localStorage.removeItem('key') can be used instead 
    //! localStorage.clear()
    localStorage.removeItem('playerScore');
    localStorage.removeItem('computerScore');
    localStorage.removeItem('ties');

    // reset in memory objects
    player.score = 0;
    computer.score = 0;
    this.tiedRounds = 0;
    return 'All the game statistics and scores have been reset!';
  }
};

function setComputersMove(){
  const randomNumber = Math.random();
  //* ternary operator
  let computersMove = 
  randomNumber < 1/3 ? 'rock' : 
  randomNumber < 2/3 ? 'paper' : 
  'scissors';
  computer.move = computersMove;
}

function determineWinner(){
  setComputersMove();
  
  //*destructuring, can only be used if the variable name is the same as object's property name
  let { tiedRounds } = gameStats; 
  let result = `Computer picked ${computer.move}.\n`;
  if(player.move === computer.move){
    tiedRounds++;
    /* 
      toString() is a method that belongs to an object that is being called on some integer value here, 
      which is tiedRounds in this case, JS wrappes this value/variable in a special object that has
      the toString() method == //* Auto-boxing featute of JS
    */
    localStorage.setItem('ties', tiedRounds.toString());
    gameStats.tiedRounds = tiedRounds;
    result += 'It\'s a tie!';
  } else if (player.move === 'paper' && computer.move === 'rock' || player.move === 'rock' && computer.move === 'paper'){
    if(computer.move === 'paper'){
      computer.score++;
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else if (player.move === 'paper' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'paper'){
    if(computer.move === 'scissors'){
      computer.score ++;
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else if (player.move === 'rock' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'rock'){
    if(computer.move === 'rock'){
      computer.score++;
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else {
    result = `An error has occurred in determineWinner()\nPlayer: ${player.move}\nComputer: ${computer.move}`;
  }
  result += `\n${gameStats.show()}`
  return result;
}