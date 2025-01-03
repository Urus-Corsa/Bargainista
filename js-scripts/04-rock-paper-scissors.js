const player = {
  /*
    upon initilization of player object as well as its 'move' property,
    we set it to //* null == intentionally want it to be empty rather than using 'undefined'
  */
  move: localStorage.getItem('playerMove') || '',
  setMove(pickedMove){
    this.move = pickedMove.toLowerCase()
    localStorage.setItem('playerMove', pickedMove);
  },
  score: parseInt(localStorage.getItem('playerScore'), 10) || 0,
  displayCurrentScore(){
    document.querySelector('.js-stats-board .js-players-stats .js-players-score').innerText = `Score: ${this.score}`;
  },
  displayLastMove() {
    document.querySelector('.js-stats-board .js-players-stats .js-players-last-move').innerText = `Last Played: ${this.move}`;
  },
  displayStats() {
    this.displayCurrentScore()
    this.displayLastMove()
  }
};
const computer = {
  move: localStorage.getItem('computerMove') || '',
  setMove(){
    const randomNumber = Math.random();
    //* ternary operator
    let computersMove = 
    randomNumber < 1/3 ? 'rock' : 
    randomNumber < 2/3 ? 'paper' : 
    'scissors';
    this.move = computersMove;
    localStorage.setItem('computerMove', computersMove);
  },
  'score': parseInt(localStorage.getItem('computerScore'), 10) || 0,
  displayCurrentScore() {
    document.querySelector('.js-stats-board .js-computers-stats .js-computers-score').innerText = `Score: ${this.score}`;
  },
  displayLastMove() {
    document.querySelector('.js-stats-board .js-computers-stats .js-computers-last-move').innerText = `Last Played: ${this.move}`;
  },
  displayStats() {
    this.displayCurrentScore()
    this.displayLastMove()
  }
};
const gameStats = {
  ['tiedRounds']: parseInt(localStorage.getItem('ties'), 10) || 0,
  lastRoundResult: localStorage.getItem('lastRoundResult') || '',
  // when explicitly defining a method, it can also be done using //* shorthand method shortcut -> display: function  displayGameStats() {} => displayGameStats() {}
  displayFrontRunner(){
    const currentWinner = 
    player.score > computer.score ? "You're in the lead!" :
    player.score < computer.score ? "Computer's in the lead!" :
    player.score === 0 && computer.score === 0 ? '' :
    'Tie!'; 
    document.querySelector('.js-stats-board .js-game-stats .js-front-runner').innerText = `Front Runnner: ${currentWinner}`;
  },
  displayTiedRounds() {
    document.querySelector('.js-stats-board .js-game-stats .js-tied-rounds').innerText = `Tied Rounds: ${this.tiedRounds}`;
  },
  displayLastRoundResult(){
    document.querySelector('.js-stats-board .js-game-stats .js-last-round-result').innerText = `Last Round's Result: ${this.lastRoundResult}`;
  },
  displayTotalRounds() {
    document.querySelector('.js-stats-board .js-game-stats .js-total-rounds').innerText = `Total Rounds: ${player.score+computer.score+this.tiedRounds}`;
  },
  displayStats() {
    this.displayFrontRunner();
    this.displayLastRoundResult();
    this.displayTiedRounds();
    this.displayTotalRounds();
  }
};
const scoreboard = {
  display() {
    player.displayStats();
    computer.displayStats();
    gameStats.displayStats();
  },
  reset: function resetGameStats(){ //* functions stored inside of an object == methods
    // reset local storage objects
    //! clearing the entire local storage objects may result in other stored data to be lost as well, localStorage.removeItem('key') can be used instead 
    //! localStorage.clear()
    localStorage.removeItem('playerScore');
    localStorage.removeItem('playerMove');
    
    localStorage.removeItem('computerScore');
    localStorage.removeItem('computerMove');
    
    localStorage.removeItem('ties');
    localStorage.removeItem('lastRoundResult');

    // reset in memory objects
    player.score = 0;
    player.move = '';
    computer.score = 0;
    computer.move = '';
    gameStats.tiedRounds = 0;
    gameStats.lastRoundResult = '';
    return 'All the game statistics and scores have been reset!';
  }
}

function determineWinner(){
  computer.setMove();
  
  //*destructuring, can only be used if the variable name is the same as object's property name
  let { tiedRounds } = gameStats; 
  let result = `You picked ${player.move}, Computer played ${computer.move}.\n`;
  if(player.move === computer.move){
    tiedRounds++;
    gameStats.tiedRounds = tiedRounds;
    gameStats.lastRoundResult = 'Was a tie!';
    /* 
      toString() is a method that belongs to an object that is being called on some integer value here, 
      which is tiedRounds in this case, JS wrappes this value/variable in a special object that has
      the toString() method == //* Auto-boxing featute of JS
    */
    localStorage.setItem('ties', tiedRounds.toString());
    result += 'It\'s a tie!';
  } else if (player.move === 'paper' && computer.move === 'rock' || player.move === 'rock' && computer.move === 'paper'){
    if(computer.move === 'paper'){
      computer.score++;
      gameStats.lastRoundResult = 'Computer won!';
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      gameStats.lastRoundResult = 'You won!';
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else if (player.move === 'paper' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'paper'){
    if(computer.move === 'scissors'){
      computer.score ++;
      gameStats.lastRoundResult = 'Computer won!';
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      gameStats.lastRoundResult = 'You won!';
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else if (player.move === 'rock' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'rock'){
    if(computer.move === 'rock'){
      computer.score++;
      gameStats.lastRoundResult = 'Computer won!';
      localStorage.setItem('computerScore', computer.score.toString());
      result += 'Computer wins!';
    }else{
      player.score++;
      gameStats.lastRoundResult = 'You won!';
      localStorage.setItem('playerScore', player.score.toString());
      result += 'You win!';
    }
  } else {
    return `An error has occurred in determineWinner()\nPlayer: ${player.move}\nComputer: ${computer.move}`;
  }
  localStorage.setItem('lastRoundResult', gameStats.lastRoundResult);
  console.log(result);
  return result;
}